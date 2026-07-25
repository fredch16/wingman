"""Hardcoded examples for prompt tuning outside the production inbox."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PlaygroundComment:
    comment_id: str
    scenario: str
    text: str
    expected_intent: str


PLAYGROUND_COMMENTS = (
    PlaygroundComment(
        "generic-praise",
        "Generic praise",
        "Nice video!",
        "Lower priority than specific appreciation; generic_praise and usually "
        "not essential to reply.",
    ),
    PlaygroundComment(
        "heartfelt-thanks",
        "Heartfelt thanks",
        "I've struggled with control theory for months, and your explanation "
        "was the first one that made it click. Thank you for taking the time.",
        "High-value community_connection; reply-worthy despite containing no "
        "question.",
    ),
    PlaygroundComment(
        "inspired-engineer",
        "Inspired to start engineering",
        "Your robot series inspired me to apply for an engineering course. I "
        "start next month and just wanted you to know these videos mattered.",
        "High-priority community_connection because the creator's work inspired "
        "a meaningful learning step.",
    ),
    PlaygroundComment(
        "returning-viewer",
        "Returning viewer",
        "I've been here since the first balancing-robot video. It has been great "
        "watching both the project and your explanations improve each week.",
        "Reply-worthy community_connection that recognizes an ongoing viewer "
        "relationship.",
    ),
    PlaygroundComment(
        "technical-correction",
        "Technical correction",
        "At 4:12 the diagram labels the derivative gain as Ki, but it should be "
        "Kd. The explanation itself sounds correct.",
        "High-priority constructive_correction that helps the creator and future "
        "learners; no research should be needed.",
    ),
    PlaygroundComment(
        "technical-question",
        "Technical question",
        "If the derivative term reacts to the rate of change, how do you stop "
        "sensor noise from making the controller unstable?",
        "High-priority technical_question that is useful to answer and normally "
        "does not require outside research.",
    ),
    PlaygroundComment(
        "content-idea",
        "Content idea",
        "Could you make a follow-up comparing a PID controller with model "
        "predictive control on the same robot?",
        "Reply-worthy content_idea with strong usefulness to the learning "
        "community.",
    ),
    PlaygroundComment(
        "spam",
        "Spam",
        "Amazing upload! Promote it on my channel and buy followers at "
        "best-growth.example.",
        "Very low priority spam; not reply-worthy.",
    ),
    PlaygroundComment(
        "first",
        "Empty first comment",
        "First.",
        "Very low priority low_value remark; not reply-worthy.",
    ),
    PlaygroundComment(
        "shared-experience",
        "Shared engineering experience",
        "We used a similar controller on our student rover and learned the hard "
        "way that wheel slip made the encoder data misleading. Your filtering "
        "section would have saved us a week.",
        "High-value community_connection or meaningful_discussion: a specific "
        "shared learning experience worth acknowledging.",
    ),
)


def get_playground_comment(comment_id: str) -> PlaygroundComment | None:
    return next(
        (comment for comment in PLAYGROUND_COMMENTS if comment.comment_id == comment_id),
        None,
    )
