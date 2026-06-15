"""Pydantic models shared across the `classify` flow.

Two kinds live here, kept together because they are pure data with no I/O:

- the LLM's structured-output schemas (`Topic`/`TopicList`,
  `Assignment`/`AssignmentList`), validated straight off model replies;
- `Entry`, the deduped, preprocessed view of a single tab (id + title + url +
  domain) that the rest of the pipeline passes around.
"""

from pydantic import BaseModel, Field


class Topic(BaseModel):
    name: str = Field(description="Short English topic name.")
    description: str = Field(
        default="", description="One-line classification criteria."
    )


class TopicList(BaseModel):
    topics: list[Topic]


class Assignment(BaseModel):
    id: int
    topic: str


class AssignmentList(BaseModel):
    assignments: list[Assignment]


class Entry(BaseModel):
    id: int
    title: str
    url: str
    domain: str
