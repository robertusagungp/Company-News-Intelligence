CREATE SCHEMA IF NOT EXISTS "Company Intelligence";

CREATE TABLE IF NOT EXISTS "Company Intelligence".companies (
    company_id UUID PRIMARY KEY,
    company_name VARCHAR(255) NOT NULL,
    company_name_normalized VARCHAR(255) NOT NULL UNIQUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS "Company Intelligence".news_articles (
    article_id UUID PRIMARY KEY,
    company_id UUID NOT NULL REFERENCES "Company Intelligence".companies(company_id),
    title TEXT NOT NULL,
    content TEXT,
    source_name VARCHAR(255),
    source_url TEXT NOT NULL UNIQUE,
    published_at TIMESTAMP,
    scraped_at TIMESTAMP NOT NULL DEFAULT NOW(),
    sentiment_label VARCHAR(50),
    topic_label VARCHAR(100),
    content_hash VARCHAR(64)
);

CREATE TABLE IF NOT EXISTS "Company Intelligence".company_llm_insights (
    insight_id UUID PRIMARY KEY,
    company_id UUID NOT NULL REFERENCES "Company Intelligence".companies(company_id),
    generated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    input_summary TEXT NOT NULL,
    insight_text TEXT,
    recommendation_text TEXT
);

CREATE INDEX IF NOT EXISTS idx_news_articles_company_id
ON "Company Intelligence".news_articles(company_id);

CREATE INDEX IF NOT EXISTS idx_news_articles_published_at
ON "Company Intelligence".news_articles(published_at);

CREATE INDEX IF NOT EXISTS idx_news_articles_sentiment
ON "Company Intelligence".news_articles(sentiment_label);

CREATE INDEX IF NOT EXISTS idx_news_articles_topic
ON "Company Intelligence".news_articles(topic_label);
