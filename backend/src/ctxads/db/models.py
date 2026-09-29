from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBED_DIM = 384
Money = Numeric(14, 2)


class Base(DeclarativeBase):
    pass


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ChannelStatus:
    PENDING_CONSENT = "pending_consent"
    INSUFFICIENT_RIGHTS = "insufficient_rights"
    ACTIVE = "active"
    PAUSED = "paused"
    REMOVED = "removed"


class ProposalStatus:
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"
    PUBLISHED = "published"
    CANCELLED = "cancelled"


class User(Base):
    __tablename__ = "users"
    max_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    name: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = _now()


class Consent(Base):
    __tablename__ = "consents"
    __table_args__ = (UniqueConstraint("user_id", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.max_user_id", ondelete="CASCADE"))
    version: Mapped[str] = mapped_column(String(32))
    accepted_at: Mapped[datetime] = _now()


class Channel(Base):
    __tablename__ = "channels"
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    owner_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    title: Mapped[str | None] = mapped_column(String(255))
    subscribers: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(32))
    bot_permissions: Mapped[list[str]] = mapped_column(
        ARRAY(Text), default=list, server_default="{}"
    )
    profile_summary: Mapped[str | None] = mapped_column(Text)
    profile_embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ChannelSettings(Base):
    __tablename__ = "channel_settings"
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("channels.chat_id", ondelete="CASCADE"), primary_key=True
    )
    blocked_categories: Mapped[list[str]] = mapped_column(
        ARRAY(Text), default=list, server_default="{}"
    )
    allowed_categories: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    allow_regulated: Mapped[list[str]] = mapped_column(
        ARRAY(Text), default=list, server_default="{}"
    )
    min_posts_between_ads: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    min_minutes_between_ads: Mapped[int] = mapped_column(Integer, default=120, server_default="120")
    max_ads_per_day: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    publish_delay_min: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    ad_ttl_hours: Mapped[int] = mapped_column(Integer, default=48, server_default="48")


class Post(Base):
    __tablename__ = "posts"
    __table_args__ = (Index("ix_posts_chat_created", "chat_id", "created_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    mid: Mapped[str] = mapped_column(String(128), unique=True)
    text: Mapped[str] = mapped_column(Text, default="")
    text_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = _now()
    analysis: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM))
    is_our_ad: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    skip_reason: Mapped[str | None] = mapped_column(String(64))


class Advertiser(Base):
    __tablename__ = "advertisers"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    legal_name: Mapped[str] = mapped_column(String(255))
    inn: Mapped[str] = mapped_column(String(12))
    postback_secret: Mapped[str] = mapped_column(String(128))
    # Владелец кабинета в боте; у рекламодателей из seed — None.
    owner_user_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)


class Ad(Base):
    __tablename__ = "ads"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)
    advertiser_id: Mapped[int] = mapped_column(ForeignKey("advertisers.id"))
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(String(2048))
    image_url: Mapped[str | None] = mapped_column(String(2048))
    category: Mapped[str] = mapped_column(String(32), index=True)
    erid: Mapped[str] = mapped_column(String(64))
    pricing_model: Mapped[str] = mapped_column(String(8))  # cpm | cpc | cpa
    price: Mapped[Decimal] = mapped_column(Money)
    budget_total: Mapped[Decimal] = mapped_column(Money)
    budget_left: Mapped[Decimal] = mapped_column(Money)
    targeting: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM))
    created_at: Mapped[datetime] = _now()


Index(
    "ix_ads_embedding_hnsw",
    Ad.embedding,
    postgresql_using="hnsw",
    postgresql_ops={"embedding": "vector_cosine_ops"},
)


class Proposal(Base):
    __tablename__ = "proposals"
    __table_args__ = (Index("ix_proposals_chat_status", "chat_id", "status"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"))
    ad_id: Mapped[int] = mapped_column(ForeignKey("ads.id"))
    chat_id: Mapped[int] = mapped_column(BigInteger)
    score: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text, default="")
    expected_payout: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    candidates: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default="[]"
    )
    status: Mapped[str] = mapped_column(String(16), default=ProposalStatus.PENDING)
    admin_message_mid: Mapped[str | None] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    publish_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()


class Placement(Base):
    __tablename__ = "placements"
    id: Mapped[int] = mapped_column(primary_key=True)
    proposal_id: Mapped[int] = mapped_column(ForeignKey("proposals.id"), unique=True)
    ad_id: Mapped[int] = mapped_column(ForeignKey("ads.id"))
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    mid: Mapped[str | None] = mapped_column(String(128), unique=True)
    token: Mapped[str] = mapped_column(String(32), unique=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    views: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class Click(Base):
    __tablename__ = "clicks"
    __table_args__ = (Index("ix_clicks_dedupe", "placement_id", "ip_hash", "ts"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    placement_id: Mapped[int] = mapped_column(ForeignKey("placements.id"))
    click_id: Mapped[str] = mapped_column(String(64), unique=True)
    ts: Mapped[datetime] = _now()
    ip_hash: Mapped[str] = mapped_column(String(64))
    ua_hash: Mapped[str] = mapped_column(String(64))
    is_unique: Mapped[bool] = mapped_column(Boolean, default=True)


class Conversion(Base):
    __tablename__ = "conversions"
    id: Mapped[int] = mapped_column(primary_key=True)
    click_id: Mapped[str] = mapped_column(ForeignKey("clicks.click_id"), unique=True)
    amount: Mapped[Decimal] = mapped_column(Money)
    ts: Mapped[datetime] = _now()


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    placement_id: Mapped[int] = mapped_column(ForeignKey("placements.id"), index=True)
    ad_id: Mapped[int] = mapped_column(ForeignKey("ads.id"))
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    kind: Mapped[str] = mapped_column(String(8))  # cpm | cpc | cpa
    ref: Mapped[str] = mapped_column(String(128), unique=True)  # идемпотентность начислений
    advertiser_debit: Mapped[Decimal] = mapped_column(Money)
    channel_credit: Mapped[Decimal] = mapped_column(Money)
    platform_fee: Mapped[Decimal] = mapped_column(Money)
    ts: Mapped[datetime] = _now()


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_run_at", "status", "run_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    run_at: Mapped[datetime] = _now()
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()


class DialogState(Base):
    """Пошаговый ввод в личке (кабинет рекламодателя): какой шаг ждём и что уже введено."""

    __tablename__ = "dialog_states"
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    flow: Mapped[str] = mapped_column(String(32))
    step: Mapped[str] = mapped_column(String(32))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MiniAppIdempotency(Base):
    """Completed mini-app mutations, so a retried request cannot repeat side effects."""

    __tablename__ = "miniapp_idempotency"
    __table_args__ = (
        UniqueConstraint("user_id", "key_hash"),
        Index("ix_miniapp_idempotency_created_at", "created_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    operation: Mapped[str] = mapped_column(String(96), nullable=False)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _now()


class ProcessedUpdate(Base):
    __tablename__ = "processed_updates"
    dedupe_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
