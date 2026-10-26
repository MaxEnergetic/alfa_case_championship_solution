"""The stable combined JSON contract. Monetary values are in rubles."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr

from ..api_models import CompactEntity, SourceStatus
from .questions import Question
from ..scoring.models import ScoringResult

Amount = int | float
YesNo = Literal["yes", "no"] | None


class Revision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0, strict=True, description="Последняя полученная версия сессии")


class SubmitAnswer(Revision):
    question_id: str = Field(min_length=1, max_length=64, description="Уникальный next_question.id, не номер Qxx")
    value: StrictStr | StrictInt | StrictFloat | None = Field(..., description="Ответ; null означает «не знаю», если допустимо")


class AnswerItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_id: str = Field(min_length=1, max_length=64)
    value: StrictStr | StrictInt | StrictFloat | None


class SubmitAnswers(Revision):
    answers: list[AnswerItem] = Field(min_length=1, max_length=5)


class CreateSession(BaseModel):
    model_config = ConfigDict(extra="forbid")
    flow_version: Literal[1, 2] = 1


class AttachRegistry(Revision):
    job_id: str = Field(min_length=1, max_length=128)
    selected_ogrn: str | None = Field(None, pattern=r"^(?:[0-9]{13}|[0-9]{15})$")


class Borrower(BaseModel):
    registered: Literal["yes", "pending", "no"] | None = None
    form: Literal["ip", "ooo", "other_legal", "self_employed", "none"] | None = None
    owner_age: int | None = None
    citizenship: Literal["rf", "other"] | None = None
    personal_history: Literal["none", "clear", "arrears"] | None = None
    personal_history_details: str | None = None


class Financing(BaseModel):
    knowledge: Literal["yes", "no", "compare"] | None = None
    preferred: str | None = None
    goal: str | None = None
    description: str | None = None
    regular: YesNo = None
    disbursement: Literal["once", "parts"] | None = None
    reuse: YesNo = None
    amount_rub: Amount | None = None
    needed_date: str | None = None
    term_months: int | None = None
    tranche_months: int | None = None
    gap_days: int | None = None
    receivable: YesNo = None
    repayment_source: str | None = None
    source_details: str | None = None
    contract_need: str | None = None
    contract_details: str | None = None
    asset: str | None = None
    property_chosen: YesNo = None
    vehicle: str | None = None
    asset_cost_rub: Amount | None = None
    startup_costs: str | None = None


class ExistingLoan(BaseModel):
    exists: YesNo = None
    owner: str | None = None
    bank_and_goal: str | None = None
    balance_rub: Amount | None = None
    payment_monthly_rub: Amount | None = None
    remaining_months: int | None = None


class BankRelationship(BaseModel):
    alpha_client: str | None = None
    personal_offer: YesNo = None
    offer_details: str | None = None
    subscription: YesNo = None
    subscription_target: str | None = None
    subscription_valid: YesNo = None
    subscription_details: str | None = None


class Security(BaseModel):
    collateral: YesNo = None
    collateral_details: str | None = None
    guarantor: YesNo = None
    guarantor_details: str | None = None
    own_funds: YesNo = None
    own_amount_rub: Amount | None = None


class Financials(BaseModel):
    seasonal: YesNo = None
    season_details: str | None = None
    revenue_monthly_rub: Amount | None = None
    revenue_period: str | None = None
    free_cash_monthly_rub: Amount | None = None
    debt_payments_monthly_rub: Amount | None = None
    arrears: YesNo = None
    arrears_details: str | None = None


class Company(BaseModel):
    inn: str | None = None
    selected_ogrn: str | None = None
    lookup_status: Literal["not_requested", "ok", "partial", "not_found", "error"] = "not_requested"
    checked_at: str | None = None
    sources: dict[str, SourceStatus] = Field(default_factory=dict)
    entities: list[CompactEntity] = Field(default_factory=list)


class ApplicationData(BaseModel):
    borrower: Borrower
    company: Company
    financing: Financing
    existing_loan: ExistingLoan
    bank: BankRelationship
    security: Security
    financials: Financials


class ProductCandidate(BaseModel):
    code: str
    name: str
    status: Literal["candidate", "clarify", "excluded", "separate"]
    reason: str
    checks: list[str]


class Recommendation(BaseModel):
    status: Literal["R01", "R02", "R03"]
    rules_as_of: str
    is_bank_decision: Literal[False] = False
    candidates: list[ProductCandidate]
    issues: list[str]
    notes: list[str]


class SessionView(BaseModel):
    schema_version: Literal["credit-application-1.0"] = "credit-application-1.0"
    session_id: str
    revision: int
    state: Literal["collecting", "ready"]
    created_at: str
    updated_at: str
    next_question: Question | None
    question_group: list[Question] = Field(default_factory=list)
    answered_questions: list[dict] = Field(default_factory=list)
    data: ApplicationData
    answer_states: dict[str, Literal["provided", "unknown", "derived", "registry"]] = Field(
        description="Пути полей data; отсутствие ключа означает, что поле ещё не спрашивали или пропустили по ветке")
    field_sources: dict[str, "FieldSource"] = Field(default_factory=dict)
    conflicts: list["FieldConflict"] = Field(default_factory=list)
    recommendation: Recommendation | None
    scoring_job_id: str | None = None
    scoring: ScoringResult | None = None
    assessment: dict | None = None


class FieldSource(BaseModel):
    source: Literal["fns_parser"]
    checked_at: str | None
    ogrn: str


class FieldConflict(BaseModel):
    field: str
    user_value: str | int | float | None
    registry_value: str | int | float
    chosen_source: Literal["fns_parser"] = "fns_parser"


SessionView.model_rebuild()
