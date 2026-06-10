-- Database setup for r/AustralianTeachers far-right discourse study
-- Run once before first scrape: mysql -u root -p < setup_db.sql

CREATE DATABASE IF NOT EXISTS aus_teachers_reddit
    CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE aus_teachers_reddit;

-- All posts collected (no keyword pre-filtering)
CREATE TABLE IF NOT EXISTS posts (
    id               INT AUTO_INCREMENT PRIMARY KEY,
    submission_id    VARCHAR(20)  NOT NULL,
    subreddit        VARCHAR(100),
    title            TEXT,
    selftext         LONGTEXT,
    url              TEXT,
    post_url         TEXT,
    score            INT,
    upvote_ratio     FLOAT,
    num_comments     INT,
    post_author      VARCHAR(100),
    post_created_utc DATETIME,
    post_flair       VARCHAR(255),
    is_self          BOOLEAN,
    scrape_date      DATE,
    UNIQUE KEY unique_post (submission_id)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- All comments linked to their parent post
CREATE TABLE IF NOT EXISTS comments (
    id                   INT AUTO_INCREMENT PRIMARY KEY,
    comment_id           VARCHAR(20)  NOT NULL,
    submission_id        VARCHAR(20)  NOT NULL,
    comment_body         LONGTEXT,
    comment_author       VARCHAR(100),
    comment_created_utc  DATETIME,
    comment_depth        INT,
    parent_id            VARCHAR(30),
    comment_score        INT,
    comment_url          TEXT,
    scrape_date          DATE,
    UNIQUE KEY unique_comment (comment_id),
    INDEX idx_submission (submission_id)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- Maps posts to the keyword searches that surfaced them
-- Use for post-hoc analysis: JOIN posts <- search_matches on submission_id
CREATE TABLE IF NOT EXISTS search_matches (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    submission_id VARCHAR(20)  NOT NULL,
    category      VARCHAR(100),
    search_term   VARCHAR(255),
    scrape_date   DATE,
    UNIQUE KEY unique_match (submission_id, search_term),
    INDEX idx_category (category),
    INDEX idx_term (search_term)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
