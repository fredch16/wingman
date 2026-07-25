"""Persist classifications for real inbox comments using the shared service."""

from dataclasses import dataclass

from classification_service import ClassificationService, CommentClassification
from inbox_repository import CommentRepository, utc_now

CLASSIFICATION_VERSION = "production-v1"


@dataclass(frozen=True)
class ClassificationRecord:
    result: CommentClassification
    classified_at: str
    classification_model: str
    classification_version: str


def classify_stored_comment(
    repository: CommentRepository,
    service: ClassificationService,
    comment_id: str,
    *,
    persist: bool = True,
) -> ClassificationRecord:
    """Classify one stored comment, optionally persisting the result."""
    comment = repository.get_comment(comment_id)
    if comment is None:
        raise LookupError(f"Comment not found: {comment_id}")

    result = service.classify_comment(comment)
    record = ClassificationRecord(
        result=result,
        classified_at=utc_now(),
        classification_model=service.model,
        classification_version=CLASSIFICATION_VERSION,
    )
    if persist:
        repository.save_classification(
            comment_id,
            category=result.category,
            priority=result.priority,
            reply_worthy=result.reply_worthy,
            needs_research=result.needs_research,
            reason=result.reason,
            classification_model=record.classification_model,
            classification_version=record.classification_version,
            classified_at=record.classified_at,
        )
    return record
