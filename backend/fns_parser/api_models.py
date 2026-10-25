"""HTTP response schemas; optional compact fields stay omitted on the wire."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ResultStatus = Literal["ok", "partial", "error", "not_found"]
Amount = int | float


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: list[dict[str, str]] | None = None


class ApiError(BaseModel):
    status: Literal["error"] = "error"
    error: ErrorDetail


class SourceStatus(BaseModel):
    model_config = ConfigDict(extra="allow")
    status: Literal["ok", "partial", "error", "not_found", "not_applicable", "manual_required"]
    error: ErrorDetail | None = None


class SourceIssue(SourceStatus):
    source: str
    warnings: list[str] | None = None


class Manager(BaseModel):
    name: str
    position: str | None = None


class Founder(BaseModel):
    name: str
    share_percent: Amount | None = None


class Employees(BaseModel):
    year: int
    count: Amount | None = None


class FinancialYear(BaseModel):
    year: int
    income: Amount | None = Field(None, description="Совокупный доход набора ФНС, рубли; не выручка")
    expenses: Amount | None = Field(None, description="Расходы, рубли")
    taxes_paid: Amount | None = Field(None, description="Сумма опубликованных налоговых платежей, рубли")


class TaxRegime(BaseModel):
    period: str | None = None
    regimes: list[str] | None = None
    status: Literal["no_data"] | None = None


class TaxDebt(BaseModel):
    period: str | None = None
    status: Literal["available", "no_data", "incomplete"]
    amount: Amount | None = Field(None, description="Сумма задолженности, рубли; только при available")


class AccountingYear(BaseModel):
    model_config = ConfigDict(extra="allow")
    year: int
    revenue_rub: Amount | None = None
    net_profit_rub: Amount | None = None
    assets_rub: Amount | None = None
    equity_rub: Amount | None = None
    long_term_borrowings_rub: Amount | None = None
    short_term_borrowings_rub: Amount | None = None
    accounts_payable_rub: Amount | None = None
    borrowings_rub: Amount | None = Field(None, description="Заёмные средства из двух заполненных строк 1410 + 1510; не кредитная история")


class PublicSourceResult(SourceStatus):
    url: str
    checked_at: str
    reason: str | None = None
    lookup_performed: bool | None = None
    query_scope: str | None = None
    query_inn: str | None = None
    years: list[AccountingYear] | None = None
    total_cases: int | None = None
    total_found: int | None = None
    retrieved_count: int | None = None
    truncated: bool | None = None
    cases: list[dict[str, Any]] | None = None
    proceedings: list[dict[str, Any]] | None = None


class CompactEntity(BaseModel):
    model_config = ConfigDict(extra="allow")
    inn: str | None = None
    ogrn: str
    entity_type: Literal["legal", "individual"]
    name: str | None = None
    kpp: str | None = None
    status: str | None = None
    registered_on: str | None = None
    terminated_on: str | None = None
    registration_invalid_on: str | None = None
    address: str | None = None
    main_okved: str | None = None
    capital_rub: Amount | None = None
    managers: list[Manager] | None = None
    founders: list[Founder] | None = None
    employees: Employees | None = None
    financials: list[FinancialYear] | None = None
    tax_regime: TaxRegime | None = None
    tax_debt: TaxDebt | None = None
    data_dates: dict[str, str] | None = None
    issues: list[SourceIssue] | None = None
    public_sources: dict[str, PublicSourceResult] | None = None


class CompactResult(BaseModel):
    schema_version: Literal["compact-1.0"]
    query: str
    status: ResultStatus
    checked_at: str
    sources: dict[str, SourceStatus]
    entities: list[CompactEntity]


class FullResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    schema_version: Literal["1.0"]
    query: str
    query_type: str
    started_at: str
    finished_at: str
    status: ResultStatus
    # Raw FNS fields are deliberately unrestricted and are never filtered.
    sources: dict[str, dict[str, Any]]
    entities: list[dict[str, Any]]


ParserResult = Annotated[CompactResult | FullResult, Field(discriminator="schema_version")]


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
