# -*- coding: utf-8 -*-
"""
PChome vs MOMO 商品價格比較 (Streamlit)
- 輸入商品名稱與爬取頁數
- 同時爬取 PChome 與 momo 的搜尋結果
- 比較圖表：折線圖、柱狀圖、圓餅圖、旭日圖 (Plotly)

執行方式:
    pip install -r requirements.txt
    streamlit run app.py

中文字型:
    使用 Plotly，圖表由瀏覽器繪製並使用系統內建中文字型，
    不需要 !wget 下載字型。
"""

import re
import time

import pandas as pd
import plotly.express as px
import requests as std_requests
import streamlit as st
from curl_cffi import requests as cffi_requests  # 模擬 Chrome 的 TLS 指紋 (momo 用)

# ----------------------------------------------------------------------
# 基本設定
# ----------------------------------------------------------------------
st.set_page_config(page_title="PChome vs MOMO 價格比較", page_icon="⚖️", layout="wide")

CHART_FONT = "Noto Sans TC, Microsoft JhengHei, PingFang TC, sans-serif"
PLATFORM_COLORS = {"PChome": "#1F77B4", "MOMO": "#E8528A"}

# PChome
PCHOME_API = "https://ecshweb.pchome.com.tw/search/v3.3/all/results"
PCHOME_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}
PCHOME_SORT = {
    "熱銷程度 (高→低)": "sale/dc",
    "價格 (低→高)": "prc/ac",
    "價格 (高→低)": "prc/dc",
    "相關度": "rnk/dc",
}

# MOMO
MOMO_HOME = "https://www.momoshop.com.tw/"
MOMO_API = "https://apisearch.momoshop.com.tw/momoSearchCloud/moec/textSearch"
MOMO_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": "https://www.momoshop.com.tw",
    "Referer": "https://www.momoshop.com.tw/",
}

PRICE_BINS = [0, 500, 1000, 2000, 5000, 10000, float("inf")]
PRICE_LABELS = ["0-500", "501-1000", "1001-2000", "2001-5000", "5001-10000", "10000以上"]


# ----------------------------------------------------------------------
# 爬蟲：每個函式回傳 (DataFrame, 訊息清單)
# ----------------------------------------------------------------------
def crawl_pchome(keyword, pages, sort, delay, tick):
    rows, notes = [], []
    for i in range(1, pages + 1):
        tick(f"PChome：第 {i} / {pages} 頁")
        try:
            resp = std_requests.get(
                PCHOME_API,
                params={"q": keyword, "page": i, "sort": sort},
                headers=PCHOME_HEADERS,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            notes.append(("warning", f"PChome 第 {i} 頁抓取失敗：{e}"))
            break

        prods = data.get("prods") or []
        if not prods:
            notes.append(("info", f"PChome 第 {i} 頁沒有資料，已停止。"))
            break

        for p in prods:
            rows.append({"平台": "PChome", "品名": p.get("name", ""),
                         "價格": p.get("price"), "頁數": i})

        total_page = data.get("totalPage")
        if total_page and i >= total_page:
            notes.append(("info", f"PChome 搜尋結果共 {total_page} 頁，已爬完全部。"))
            break
        if i < pages:
            time.sleep(delay)
    return pd.DataFrame(rows), notes


def momo_payload(keyword, page):
    return {
        "host": "momoshop",
        "flag": "searchEngine",
        "data": {
            "searchValue": keyword,
            "searchType": "1",
            "cateLevel": "-1",
            "curPage": str(page),
            "priceS": "0",
            "priceE": "9999999",
            "showType": "chessboardType",
            "isFuzzy": "0",
            "rtnCateDatainfo": {
                "cateCode": "",
                "cateLv": "-1",
                "keyword": keyword,
                "curPage": str(page),
                "historyDoPush": "false",
                "timestamp": int(time.time() * 1000),
            },
            "flag": 2018,
            "serviceCode": "MT01",
            "addressSearchData": {},
        },
    }


def crawl_momo(keyword, pages, delay, tick):
    rows, notes = [], []
    session = cffi_requests.Session(impersonate="chrome")
    try:
        session.get(MOMO_HOME, headers=MOMO_HEADERS, timeout=15)  # 先取得 cookie
    except Exception as e:
        return pd.DataFrame(), [("warning", f"無法連線到 momo 首頁：{e}")]

    for i in range(1, pages + 1):
        tick(f"MOMO：第 {i} / {pages} 頁")
        try:
            resp = session.post(MOMO_API, headers=MOMO_HEADERS,
                                json=momo_payload(keyword, i), timeout=15)
        except Exception as e:
            notes.append(("warning", f"MOMO 第 {i} 頁請求失敗：{e}"))
            break

        if resp.status_code != 200:
            notes.append(("warning", f"MOMO 第 {i} 頁回應狀態碼 {resp.status_code}：{resp.text[:150]}"))
            break

        try:
            products = resp.json().get("rtnSearchData", {}).get("goodsInfoList", [])
        except ValueError:
            notes.append(("warning", f"MOMO 第 {i} 頁回傳內容不是 JSON，可能被網站擋下。"))
            break

        if not products:
            notes.append(("info", f"MOMO 第 {i} 頁沒有資料，已停止。"))
            break

        for p in products:
            rows.append({"平台": "MOMO", "品名": p.get("goodsName", ""),
                         "價格": p.get("goodsPrice", ""), "頁數": i})
        if i < pages:
            time.sleep(delay)
    return pd.DataFrame(rows), notes


# ----------------------------------------------------------------------
# 資料處理
# ----------------------------------------------------------------------
def parse_price(value) -> float:
    """'$1,273' / 1273 / '$100~$200' → 數字（區間取第一個數字）。"""
    m = re.search(r"\d[\d,]*", str(value))
    return float(m.group().replace(",", "")) if m else float("nan")


def extract_brand(name) -> str:
    """從『【Anker】xxx』抽出品牌；抓不到則為『其他』。"""
    m = re.search(r"[【\[「](.+?)[】\]」]", str(name))
    return m.group(1).strip() if m else "其他"


def prepare(df: pd.DataFrame, top_brands: int) -> pd.DataFrame:
    df = df.copy()
    df["價格"] = df["價格"].map(parse_price)
    df = df.dropna(subset=["品名", "價格"])
    df = df[df["價格"] > 0].reset_index(drop=True)
    if df.empty:
        return df
    df["價格"] = df["價格"].astype(int)

    df["價格區間"] = pd.cut(
        df["價格"], bins=PRICE_BINS, labels=PRICE_LABELS, right=True, include_lowest=True
    ).astype(str)

    # 品牌：忽略大小寫合併 (Anker / ANKER)
    brand = df["品名"].map(extract_brand)
    lower = brand.str.lower()
    canon = brand.groupby(lower).agg(lambda s: s.value_counts().index[0])
    brand = lower.map(canon)
    top = brand[brand != "其他"].value_counts().head(top_brands).index
    df["品牌"] = brand.where(brand.isin(top), "其他")

    df["序號"] = df.groupby("平台").cumcount() + 1  # 各平台內的排名
    return df


def make_color_map(categories) -> dict:
    """讓兩個圓餅圖中同一類別用同一種顏色。"""
    palette = (px.colors.qualitative.Plotly + px.colors.qualitative.D3
               + px.colors.qualitative.Set3)
    return {c: palette[i % len(palette)] for i, c in enumerate(categories)}


# ----------------------------------------------------------------------
# 側邊欄
# ----------------------------------------------------------------------
st.title("⚖️ PChome vs MOMO 商品價格比較")
st.caption("輸入商品名稱與頁數，同時爬取兩個平台並比較價格分布。")

with st.sidebar:
    st.header("搜尋設定")
    keyword = st.text_input("商品名稱", value="行動電源")
    pages = st.number_input("每個平台爬取頁數", min_value=1, max_value=20, value=2, step=1)
    platforms = st.multiselect("比較平台", ["PChome", "MOMO"], default=["PChome", "MOMO"])
    sort_label = st.selectbox("PChome 排序方式", list(PCHOME_SORT.keys()))
    delay = st.slider("每頁間隔秒數", 0.0, 10.0, 2.0, 0.5,
                      help="建議至少 1 秒以上，避免被網站封鎖。")
    top_brands = st.slider("品牌顯示數量", 3, 20, 8, help="其餘品牌合併為『其他』。")
    run = st.button("🔍 開始爬取並比較", type="primary")

# ----------------------------------------------------------------------
# 爬取（按下按鈕才執行，結果存在 session_state）
# ----------------------------------------------------------------------
if run:
    if not keyword.strip():
        st.error("請輸入商品名稱。")
    elif not platforms:
        st.error("請至少選擇一個平台。")
    else:
        kw_in = keyword.strip()
        total_steps = int(pages) * len(platforms)
        bar = st.progress(0.0, text="準備中…")
        counter = {"n": 0}

        def tick(msg):
            bar.progress(min(counter["n"] / total_steps, 1.0), text=msg)
            counter["n"] += 1

        frames, all_notes = [], []
        if "PChome" in platforms:
            f, n = crawl_pchome(kw_in, int(pages), PCHOME_SORT[sort_label], delay, tick)
            frames.append(f)
            all_notes += n
            counter["n"] = max(counter["n"], int(pages))  # 提早結束時補齊進度
        if "MOMO" in platforms:
            f, n = crawl_momo(kw_in, int(pages), delay, tick)
            frames.append(f)
            all_notes += n

        bar.empty()
        frames = [f for f in frames if not f.empty]
        for level, text in all_notes:
            (st.warning if level == "warning" else st.info)(text)

        if not frames:
            st.session_state.pop("raw", None)
            st.error("沒有抓到任何資料，請換個關鍵字或稍後再試。")
        else:
            st.session_state["raw"] = pd.concat(frames, ignore_index=True)
            st.session_state["keyword"] = kw_in

if "raw" not in st.session_state:
    st.info("👈 請在左側輸入商品名稱與頁數，然後按下「開始爬取並比較」。")
    st.stop()

kw = st.session_state["keyword"]
raw = st.session_state["raw"]
df = prepare(raw, top_brands)

if df.empty:
    st.error("資料中沒有可用的商品名稱與價格。")
    st.stop()

plat_list = [p for p in ["PChome", "MOMO"] if p in df["平台"].unique()]
if len(plat_list) < 2:
    st.warning(f"目前只有 {plat_list[0]} 有資料，圖表只會顯示單一平台。")

# ----------------------------------------------------------------------
# 統計總表
# ----------------------------------------------------------------------
st.subheader(f"「{kw}」價格統計")
stats = (
    df.groupby("平台")["價格"]
    .agg(商品數="count", 平均價格="mean", 中位數="median", 最高價="max", 最低價="min")
    .round(0).astype(int).reindex(plat_list)
)
st.dataframe(stats)

if len(plat_list) == 2:
    avg = df.groupby("平台")["價格"].mean()
    cheaper = avg.idxmin()
    other = [p for p in plat_list if p != cheaper][0]
    st.metric(f"平均價格較低的平台：{cheaper}", f"{avg[cheaper]:,.0f} 元",
              delta=f"比 {other} 低 {avg[other] - avg[cheaper]:,.0f} 元", delta_color="off")
    st.caption("提醒：兩個平台的排序與搜尋邏輯不同（PChome 可選排序，momo 為預設排序），"
               "且只爬取前幾頁，結果僅供參考，不代表整體市場價格。")

tab_line, tab_bar, tab_pie, tab_sun, tab_data = st.tabs(
    ["📈 折線圖", "📊 柱狀圖", "🥧 圓餅圖", "🌞 旭日圖", "📋 資料表"]
)

# ---- 折線圖 ----
with tab_line:
    n_max = int(df.groupby("平台").size().max())
    n_show = st.slider("每個平台顯示前 N 筆商品", 1, n_max, min(70, n_max)) if n_max > 1 else 1
    plot_df = df[df["序號"] <= n_show]

    fig_line = px.line(
        plot_df, x="序號", y="價格", color="平台", markers=True,
        color_discrete_map=PLATFORM_COLORS,
        hover_data={"品名": True, "序號": False, "價格": ":,.0f"},
        title=f"「{kw}」各平台商品售價（依搜尋結果順序）",
    )
    for p in plat_list:
        m = df.loc[df["平台"] == p, "價格"].mean()
        fig_line.add_hline(
            y=m, line_dash="dash", line_color=PLATFORM_COLORS[p],
            annotation_text=f"{p} 平均 {m:,.0f}", annotation_position="top left",
        )
    fig_line.update_layout(font_family=CHART_FONT, xaxis_title="商品序號", yaxis_title="價格", height=550)
    st.plotly_chart(fig_line)

# ---- 柱狀圖 ----
with tab_bar:
    bar_kind = st.radio("比較項目", ["價格統計", "價格區間分布", "品牌商品數"], horizontal=True)

    if bar_kind == "價格統計":
        s = (df.groupby("平台")["價格"]
             .agg(平均="mean", 中位數="median", 最高="max", 最低="min")
             .round(0).reset_index()
             .melt(id_vars="平台", var_name="指標", value_name="價格"))
        fig_bar = px.bar(
            s, x="指標", y="價格", color="平台", barmode="group", text_auto=",.0f",
            color_discrete_map=PLATFORM_COLORS, title=f"「{kw}」價格統計比較",
        )

    elif bar_kind == "價格區間分布":
        mode = st.radio("顯示方式", ["商品數量", "佔該平台比例 (%)"], horizontal=True)
        c = df.groupby(["平台", "價格區間"]).size().reset_index(name="數量")
        if mode.startswith("佔"):
            c["數量"] = (c["數量"] / c.groupby("平台")["數量"].transform("sum") * 100).round(1)
        fig_bar = px.bar(
            c, x="價格區間", y="數量", color="平台", barmode="group", text_auto=True,
            color_discrete_map=PLATFORM_COLORS,
            category_orders={"價格區間": PRICE_LABELS},
            title=f"「{kw}」各價格區間{'商品數' if mode == '商品數量' else '比例'}",
        )
        fig_bar.update_yaxes(title=mode)

    else:
        b = df[df["品牌"] != "其他"].groupby(["品牌", "平台"]).size().reset_index(name="商品數")
        if b.empty:
            st.info("商品名稱中沒有偵測到品牌（【】或「」內的文字），無法繪製品牌比較。")
            fig_bar = None
        else:
            order = b.groupby("品牌")["商品數"].sum().sort_values(ascending=False).index.tolist()
            fig_bar = px.bar(
                b, x="品牌", y="商品數", color="平台", barmode="group", text_auto=True,
                color_discrete_map=PLATFORM_COLORS,
                category_orders={"品牌": order},
                title=f"「{kw}」各品牌商品數（不含『其他』）",
            )

    if fig_bar is not None:
        fig_bar.update_layout(font_family=CHART_FONT, height=550)
        st.plotly_chart(fig_bar)

# ---- 圓餅圖 ----
with tab_pie:
    pie_by = st.radio("統計依據", ["價格區間", "品牌"], horizontal=True)
    if pie_by == "價格區間":
        cats = [x for x in PRICE_LABELS if x in set(df["價格區間"])]
    else:
        cats = df["品牌"].value_counts().index.tolist()
    cmap = make_color_map(cats)

    cols = st.columns(len(plat_list))
    for col, p in zip(cols, plat_list):
        sub = df[df["平台"] == p][pie_by].value_counts().reset_index()
        sub.columns = [pie_by, "數量"]
        fig_pie = px.pie(
            sub, names=pie_by, values="數量", hole=0.3,
            color=pie_by, color_discrete_map=cmap,
            category_orders={pie_by: cats},
            title=f"{p}：{pie_by}分布",
        )
        fig_pie.update_traces(textposition="inside", textinfo="percent+label", sort=False)
        fig_pie.update_layout(font_family=CHART_FONT, height=500, showlegend=False)
        col.plotly_chart(fig_pie)

# ---- 旭日圖 ----
with tab_sun:
    path_options = {
        "平台 → 價格區間 → 品牌": ["平台", "價格區間", "品牌"],
        "平台 → 品牌": ["平台", "品牌"],
        "平台 → 價格區間": ["平台", "價格區間"],
        "價格區間 → 平台 → 品牌": ["價格區間", "平台", "品牌"],
    }
    path_label = st.selectbox("階層順序", list(path_options.keys()))
    path = path_options[path_label]

    if path[0] == "平台":
        fig_sun = px.sunburst(
            df, path=path, color="平台",
            color_discrete_map={**PLATFORM_COLORS, "(?)": "lightgrey"},
            title=f"「{kw}」商品 {path_label}",
        )
    else:
        fig_sun = px.sunburst(df, path=path, title=f"「{kw}」商品 {path_label}")
    fig_sun.update_traces(textinfo="label+percent parent", insidetextorientation="radial")
    fig_sun.update_layout(font_family=CHART_FONT, height=700)
    st.plotly_chart(fig_sun)

# ---- 資料表 ----
with tab_data:
    view = st.radio("篩選平台", ["全部"] + plat_list, horizontal=True)
    show = df if view == "全部" else df[df["平台"] == view]
    st.dataframe(show[["平台", "品名", "價格", "價格區間", "品牌", "頁數"]], height=450)

    st.download_button(
        "⬇️ 下載比較資料 (CSV)",
        data=df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
        file_name=f"{time.strftime('%Y%m%d')}_PChome_MOMO_{kw}.csv",
        mime="text/csv",
    )
