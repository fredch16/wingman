"""Persist classifications for real inbox comments using the shared service."""

from classification_service import ClassificationService, CommentClassification
from inbox_repository import CommentRepository

CLASSIFICATION_VERSION = "production-v1"


def classify_stored_comment(
    repository: CommentRepository,
    service: ClassificationService,
    comment_id: str,
) -> CommentClassification:
    """Classify one stored comment and persist the confirmed structured result."""
    comment = repository.get_comment(comment_id)
    if comment is None:
        raise LookupError(f"Comment not found: {comment_id}")

    result = service.classify_comment(comment)
    repository.save_classification(
        comment_id,
        category=result.category,
        priority=result.priority,
        reply_worthy=result.reply_worthy,
        needs_research=result.needs_research,
        reason=result.reason,
        classification_model=service.model,
        classification_version=CLASSIFICATION_VERSION,
    )
    return result
