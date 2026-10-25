"""Scoring contract: financial state classification, separate from credit decisions."""
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ScoringOptions(BaseModel):
    model_config = ConfigDict(extra='forbid')
    year: int | None = Field(None, ge=2011, le=2100)
    as_of: date | None = None
    revision: str | None = Field(None, pattern=r'^(?:main|[0-9a-f]{40})$')


class StartScoring(ScoringOptions):
    identifier: str = Field(min_length=10, max_length=13)
    registry_job_id: str | None = Field(None, min_length=1, max_length=128)


class SessionScoring(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0, strict=True)
    year: int | None = Field(None, ge=2011, le=2100)
    as_of: date | None = None
    source_revision: str | None = Field(None, pattern=r'^(?:main|[0-9a-f]{40})$')


class Prediction(BaseModel):
    model_config = ConfigDict(extra='allow')
    probability_financial_stability: float = Field(ge=0, le=1, allow_inf_nan=False)
    probability_financial_distress: float = Field(ge=0, le=1, allow_inf_nan=False,
        description='Дополнение вероятности класса устойчивости; не вероятность банковского дефолта')
    predicted_class: Literal[0, 1]
    threshold: float = Field(ge=0, le=1, allow_inf_nan=False)
    recommendation: Literal['favorable_financial_profile', 'financial_risk_review']
    meaning: str
    time_period_validation: Literal['same_cohort', 'not_validated']
    target_financial_year: int | None
    training_feature_year: int
    training_outcome_year: int


class ScoringResult(BaseModel):
    model_config = ConfigDict(extra='allow')
    schema_version: Literal['alfa_enrichment_v1'] = 'alfa_enrichment_v1'
    inn: str | None = None
    features_as_of: str
    status: Literal['ready', 'insufficient_data', 'source_unavailable', 'model_unavailable']
    can_predict: bool
    is_bank_decision: Literal[False] = False
    report_year: int | None = None
    source: dict[str, Any] = Field(default_factory=dict)
    financials_rub: dict[str, float | None] = Field(default_factory=dict)
    features: dict[str, int | float | str] = Field(default_factory=dict)
    missing_core_fields: list[str] = Field(default_factory=list)
    quality_issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    prediction: Prediction | None = None
    model: dict[str, Any] = Field(default_factory=dict)
    error: dict[str, str] | None = None
