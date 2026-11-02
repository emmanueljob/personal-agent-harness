"""Only presentation fields are exposed publicly; no resume bytes or private paths."""
from pydantic import BaseModel, Field, field_validator


class Suggestion(BaseModel):
    label: str
    question: str


class Project(BaseModel):
    id: str
    eyebrow: str
    title: str
    company: str
    description: str
    detail: str
    outcome: str
    question: str


class Career(BaseModel):
    company: str
    role: str
    dates: str
    note: str


class Portfolio(BaseModel):
    name: str = "Your name"
    shortName: str = "the owner"
    surname: str = ""
    role: str = ""
    focus: str = ""
    location: str = ""
    linkedin: str = ""
    source: str = ""
    sourceDate: str = ""
    summary: str = ""
    education: str = ""
    intro: str = "An AI guide to the work, the ideas, and the person behind them."
    footer: str = "A conversation, with a human at the heart of it."
    resumeLabel: str = "PROFILE NOT CONFIGURED"
    careerQuestion: str = "Tell me about the owner's background, using the supplied profile."
    focusLines: list[str] = Field(default_factory=list)
    suggestions: list[Suggestion] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    career: list[Career] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)

    @field_validator("linkedin")
    @classmethod
    def safe_link(cls, value):
        if value and not value.startswith("https://"):
            raise ValueError("Public links must use HTTPS")
        return value
