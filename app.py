import os
import re
import json
import uuid
import hashlib
from datetime import datetime
from email.utils import parsedate_to_datetime

import feedparser
import pandas as pd
import plotly.express as px
import psycopg2
from psycopg2.extras import RealDictCursor
import streamlit as st

try:
    from groq import Groq
except Exception:
    Groq = None


# =========================
# CONFIG
# =========================
st.set_page_config(
    page_title="Company News Intelligence MVP",
    page_icon="📰",
    layout="wide",
)

DATABASE_URL = os.getenv("DATABASE_URL", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
SCHEMA_NAME = '"Company Intelligence"'


# =========================
# DB
# =========================
def get_connection():
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL belum diset.")

    conn = psycopg2.connect(DATABASE_URL, sslmode="require")
    with conn.cursor() as cur:
        cur.execute(f"SET search_path TO {SCHEMA_NAME}, public;")
    conn.commit()
    return conn


# =========================
# TEXT UTILS
# =========================
def normalize_company_name(name: str) -> str:
    name = name.strip().lower()
    name = re.sub(r"\s+", " ", name)
    return name


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def make_hash(title: str, content: str) -> str:
    raw = f"{title}|{content}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


# =========================
# SENTIMENT
# =========================
POSITIVE_WORDS = {
    "tumbuh", "naik", "melonjak", "ekspansi", "laba", "untung", "profit",
    "investasi", "kerja sama", "kolaborasi", "inovasi", "peluncuran",
    "resmi", "membaik", "rekor", "positif", "kuat", "optimistis"
}

NEGATIVE_WORDS = {
    "turun", "anjlok", "rugi", "gugatan", "kasus", "masalah", "denda",
    "keluhan", "kontroversi", "krisis", "gagal", "penurunan", "phk",
    "negatif", "utang", "sengketa", "pelanggaran", "boikot"
}


def classify_sentiment(text: str) -> str:
    text_l = text.lower()
    pos = sum(1 for w in POSITIVE_WORDS if w in text_l)
    neg = sum(1 for w in NEGATIVE_WORDS if w in text_l)

    if pos > neg:
        return "positive"
    elif neg > pos:
        return "negative"
    return "neutral"


# =========================
# TOPIC
# =========================
TOPIC_KEYWORDS = {
    "Keuangan": ["laba", "rugi", "pendapatan", "revenue", "profit", "saham", "dividen", "keuangan"],
    "Ekspansi": ["ekspansi", "cabang", "membuka", "peresmian", "investasi", "perluasan"],
    "Produk/Layanan": ["produk", "layanan", "fitur", "peluncuran", "launching", "aplikasi", "digital"],
    "Regulasi/Hukum": ["regulasi", "hukum", "gugatan", "denda", "izin", "pemerintah", "otoritas"],
    "Reputasi/Isu": ["keluhan", "kritik", "viral", "boikot", "masalah", "kontroversi", "pengaduan"],
    "Kemitraan": ["kerja sama", "kolaborasi", "partnership", "mitra", "strategis", "mou"],
}


def classify_topic(text: str) -> str:
    text_l = text.lower()
    best_topic = "Lainnya"
    best_score = 0

    for topic, keywords in TOPIC_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_l)
        if score > best_score:
            best_score = score
            best_topic = topic

    return best_topic


# =========================
# SCRAPER (Google News RSS)
# =========================
def parse_google_news_date(entry):
    published = entry.get("published", "")
    if not published:
        return None
    try:
        return parsedate_to_datetime(published)
    except Exception:
        return None


def scrape_company_news(company_name: str, max_items: int = 500):
    """
    MVP simple source:
    Google News RSS search by company name.
    Catatan: ini bagus untuk MVP/recent news,
    tapi bukan archive penuh 2023-sekarang.
    """
    query = company_name.strip().replace(" ", "+")
    rss_url = f"https://news.google.com/rss/search?q={query}+when:3650d&hl=id&gl=ID&ceid=ID:id"

    feed = feedparser.parse(rss_url)
    articles = []

    for entry in feed.entries[:max_items]:
        title = clean_text(entry.get("title", ""))
        source_url = entry.get("link", "")
        source_name = ""

        if "source" in entry and entry["source"]:
            source_name = entry["source"].get("title", "")

        content = clean_text(entry.get("summary", ""))
        published_at = parse_google_news_date(entry)

        if not title or not source_url:
            continue

        full_text = f"{title} {content}".strip()
        sentiment = classify_sentiment(full_text)
        topic = classify_topic(full_text)
        content_hash = make_hash(title, content)

        articles.append({
            "title": title,
            "content": content,
            "source_name": source_name,
            "source_url": source_url,
            "published_at": published_at,
            "sentiment_label": sentiment,
            "topic_label": topic,
            "content_hash": content_hash,
        })

    return articles


# =========================
# DB QUERIES
# =========================
def get_or_create_company(conn, company_name: str):
    normalized = normalize_company_name(company_name)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT company_id, company_name, company_name_normalized
            FROM companies
            WHERE company_name_normalized = %s
            """,
            (normalized,)
        )
        row = cur.fetchone()

        if row:
            return row

        company_id = str(uuid.uuid4())
        cur.execute(
            """
            INSERT INTO companies (company_id, company_name, company_name_normalized)
            VALUES (%s, %s, %s)
            RETURNING company_id, company_name, company_name_normalized
            """,
            (company_id, company_name.strip(), normalized)
        )
        conn.commit()
        return cur.fetchone()


def save_articles(conn, company_id: str, articles: list):
    inserted = 0

    with conn.cursor() as cur:
        for a in articles:
            article_id = str(uuid.uuid4())
            try:
                cur.execute(
                    """
                    INSERT INTO news_articles (
                        article_id, company_id, title, content, source_name, source_url,
                        published_at, sentiment_label, topic_label, content_hash
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (source_url) DO NOTHING
                    """,
                    (
                        article_id,
                        company_id,
                        a["title"],
                        a["content"],
                        a["source_name"],
                        a["source_url"],
                        a["published_at"],
                        a["sentiment_label"],
                        a["topic_label"],
                        a["content_hash"],
                    )
                )
                if cur.rowcount > 0:
                    inserted += 1
            except Exception:
                conn.rollback()
                raise

    conn.commit()
    return inserted


def get_articles_by_company(conn, company_id: str):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
                article_id,
                title,
                content,
                source_name,
                source_url,
                published_at,
                scraped_at,
                sentiment_label,
                topic_label
            FROM news_articles
            WHERE company_id = %s
            ORDER BY published_at DESC NULLS LAST, scraped_at DESC
            """,
            (company_id,)
        )
        return cur.fetchall()


def get_latest_insight(conn, company_id: str):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT insight_text, recommendation_text, generated_at, input_summary
            FROM company_llm_insights
            WHERE company_id = %s
            ORDER BY generated_at DESC
            LIMIT 1
            """,
            (company_id,)
        )
        return cur.fetchone()


def save_insight(conn, company_id: str, input_summary: str, insight_text: str, recommendation_text: str):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO company_llm_insights (
                insight_id, company_id, input_summary, insight_text, recommendation_text
            )
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                str(uuid.uuid4()),
                company_id,
                input_summary,
                insight_text,
                recommendation_text,
            )
        )
    conn.commit()


# =========================
# HOT TOPIC AGGREGATION
# =========================
def build_summary_payload(company_name: str, articles: list):
    df = pd.DataFrame(articles)

    if df.empty:
        return {
            "company_name": company_name,
            "total_news": 0,
            "sentiment_summary": {},
            "top_topics": [],
            "hot_headlines": [],
        }

    sentiment_summary = (
        df["sentiment_label"]
        .value_counts()
        .to_dict()
    )

    top_topics_df = (
        df["topic_label"]
        .value_counts()
        .reset_index()
    )
    top_topics_df.columns = ["topic", "count"]

    top_topics = top_topics_df.head(5).to_dict(orient="records")
    hot_headlines = df["title"].dropna().head(5).tolist()

    return {
        "company_name": company_name,
        "total_news": int(len(df)),
        "sentiment_summary": sentiment_summary,
        "top_topics": top_topics,
        "hot_headlines": hot_headlines,
    }


# =========================
# GROQ
# =========================
def generate_groq_insight(summary_payload: dict):
    if not GROQ_API_KEY:
        return {
            "insight": "GROQ_API_KEY belum diset, jadi insight AI belum digenerate.",
            "recommendation": "Set environment variable GROQ_API_KEY untuk mengaktifkan insight dan rekomendasi otomatis."
        }

    if Groq is None:
        return {
            "insight": "Package groq belum terpasang.",
            "recommendation": "Pastikan requirements.txt sudah ter-install dengan benar."
        }

    client = Groq(api_key=GROQ_API_KEY)

    prompt = f"""
You are a business news analyst.

Analyze the company news summary below and produce:
1. A short insight in 3-4 sentences
2. A short recommendation in 2-3 sentences

Rules:
- Base the answer only on the provided news summary
- Do not invent facts
- Focus on business movement, reputation, and public narrative
- Keep it concise and professional
- Return valid JSON only

News Summary:
{json.dumps(summary_payload, ensure_ascii=False, indent=2)}

Return exactly:
{{
  "insight": "...",
  "recommendation": "..."
}}
"""

    resp = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": "You are a precise business intelligence assistant."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
    )

    content = resp.choices[0].message.content.strip()

    try:
        parsed = json.loads(content)
        return {
            "insight": parsed.get("insight", ""),
            "recommendation": parsed.get("recommendation", ""),
        }
    except Exception:
        return {
            "insight": content,
            "recommendation": ""
        }


# =========================
# STREAMLIT UI
# =========================
st.title("📰 Company News Intelligence MVP")
st.caption("MVP 1 — Streamlit + Neon PostgreSQL + Google News RSS + rule-based NLP + Groq insight")

with st.expander("Environment variables yang wajib"):
    st.code(
        "DATABASE_URL=postgresql://user:password@host/dbname?sslmode=require\n"
        "GROQ_API_KEY=your_groq_api_key"
    )

company_name = st.text_input("Masukkan nama perusahaan", placeholder="Contoh: Bank Central Asia")

col1, col2 = st.columns([1, 1])
search_clicked = col1.button("Cari / Ambil Berita", use_container_width=True)
refresh_ai_clicked = col2.button("Refresh Insight AI", use_container_width=True)

if search_clicked or refresh_ai_clicked:
    if not company_name.strip():
        st.warning("Masukkan nama perusahaan dulu.")
        st.stop()

    try:
        conn = get_connection()
    except Exception as e:
        st.error(f"Gagal koneksi DB: {e}")
        st.stop()

    try:
        company = get_or_create_company(conn, company_name)
        company_id = company["company_id"]

        if search_clicked:
            with st.spinner("Mengambil berita dari source terbatas..."):
                scraped_articles = scrape_company_news(company_name, max_items=500)
                inserted_count = save_articles(conn, company_id, scraped_articles)
                st.success(f"Scraping selesai. Artikel baru tersimpan: {inserted_count}")

        articles = get_articles_by_company(conn, company_id)

        if not articles:
            st.info("Belum ada artikel untuk perusahaan ini.")
            st.stop()

        summary_payload = build_summary_payload(company_name, articles)

        latest_insight = get_latest_insight(conn, company_id)

        if refresh_ai_clicked or latest_insight is None:
            with st.spinner("Membuat insight & rekomendasi dari Groq..."):
                groq_result = generate_groq_insight(summary_payload)
                save_insight(
                    conn,
                    company_id,
                    json.dumps(summary_payload, ensure_ascii=False),
                    groq_result["insight"],
                    groq_result["recommendation"],
                )
                latest_insight = get_latest_insight(conn, company_id)

        df = pd.DataFrame(articles)
        if "published_at" in df.columns:
            df["published_at"] = pd.to_datetime(df["published_at"], errors="coerce")
            df["publish_date"] = df["published_at"].dt.date

        st.subheader(f"Dashboard — {company_name}")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Berita", len(df))
        c2.metric("Positive", int((df["sentiment_label"] == "positive").sum()))
        c3.metric("Neutral", int((df["sentiment_label"] == "neutral").sum()))
        c4.metric("Negative", int((df["sentiment_label"] == "negative").sum()))

        left, right = st.columns(2)

        with left:
            st.markdown("### Distribusi Sentiment")
            sentiment_counts = df["sentiment_label"].value_counts().reset_index()
            sentiment_counts.columns = ["sentiment", "count"]
            fig_sentiment = px.pie(sentiment_counts, names="sentiment", values="count")
            st.plotly_chart(fig_sentiment, use_container_width=True)

        with right:
            st.markdown("### Top Topic")
            topic_counts = df["topic_label"].value_counts().reset_index()
            topic_counts.columns = ["topic", "count"]
            fig_topics = px.bar(topic_counts, x="topic", y="count")
            st.plotly_chart(fig_topics, use_container_width=True)

        st.markdown("### Tren Berita per Tanggal")
        if "publish_date" in df.columns:
            trend_df = df.groupby("publish_date").size().reset_index(name="count")
            fig_trend = px.line(trend_df, x="publish_date", y="count", markers=True)
            st.plotly_chart(fig_trend, use_container_width=True)
        else:
            st.info("Tanggal publish tidak tersedia untuk chart tren.")

        st.markdown("### Insight AI")
        st.info(latest_insight["insight_text"] if latest_insight else "Belum ada insight.")

        st.markdown("### Recommendation")
        st.success(latest_insight["recommendation_text"] if latest_insight else "Belum ada recommendation.")

        st.markdown("### Hot Headlines")
        for h in summary_payload["hot_headlines"]:
            st.write(f"- {h}")

        st.markdown("### Detail Artikel")
        show_df = df.copy()
        if "source_url" in show_df.columns:
            show_df["source_url"] = show_df["source_url"].astype(str)

        cols = [
            "published_at",
            "title",
            "source_name",
            "sentiment_label",
            "topic_label",
            "source_url",
        ]
        cols = [c for c in cols if c in show_df.columns]
        st.dataframe(show_df[cols], use_container_width=True)

    except Exception as e:
        st.error(f"Terjadi error: {e}")
    finally:
        conn.close()
