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
        "generic_praise; approximately 0.20-0.40, low priority but potentially "
        "reply-worthy, with needs_research false.",
    ),
    PlaygroundComment(
        "heartfelt-thanks",
        "Heartfelt thanks",
        "I've struggled with control theory for months, and your explanation "
        "was the first one that made it click. Thank you for taking the time.",
        "community_connection; approximately 0.90-1.00, reply-worthy, with "
        "needs_research false.",
    ),
    PlaygroundComment(
        "inspired-engineer",
        "Inspired to start engineering",
        "Your robot series inspired me to apply for an engineering course. I "
        "start next month and just wanted you to know these videos mattered.",
        "community_connection; approximately 0.90-1.00 for meaningful personal "
        "impact, reply-worthy, with needs_research false.",
    ),
    PlaygroundComment(
        "returning-viewer",
        "Returning viewer",
        "I've been here since the first balancing-robot video. It has been great "
        "watching both the project and your explanations improve each week.",
        "community_connection; approximately 0.70-0.89, reply-worthy, with "
        "needs_research false.",
    ),
    PlaygroundComment(
        "technical-correction",
        "Technical correction",
        "At 4:12 the diagram labels the derivative gain as Ki, but it should be "
        "Kd. The explanation itself sounds correct.",
        "constructive_correction; approximately 0.90-1.00 because video accuracy "
        "is affected, reply-worthy, with needs_research false.",
    ),
    PlaygroundComment(
        "technical-question",
        "Technical question",
        "If the derivative term reacts to the rate of change, how do you stop "
        "sensor noise from making the controller unstable?",
        "technical_question; approximately 0.80-0.95, reply-worthy, with "
        "needs_research false.",
    ),
    PlaygroundComment(
        "content-idea",
        "Content idea",
        "Could you make a follow-up comparing a PID controller with model "
        "predictive control on the same robot?",
        "content_idea; approximately 0.70-0.89, reply-worthy, and needs_research "
        "false because only the immediate acknowledgement is assessed.",
    ),
    PlaygroundComment(
        "spam",
        "Spam",
        "Amazing upload! Promote it on my channel and buy followers at "
        "best-growth.example.",
        "spam; approximately 0.00-0.09, not reply-worthy, with needs_research false.",
    ),
    PlaygroundComment(
        "first",
        "Empty first comment",
        "First.",
        "low_value; approximately 0.00-0.09, not reply-worthy, with needs_research "
        "false.",
    ),
    PlaygroundComment(
        "shared-experience",
        "Shared engineering experience",
        "We used a similar controller on our student rover and learned the hard "
        "way that wheel slip made the encoder data misleading. Your filtering "
        "section would have saved us a week.",
        "community_connection or meaningful_discussion; approximately 0.75-0.90, "
        "reply-worthy, with needs_research false.",
    ),
)


def get_playground_comment(comment_id: str) -> PlaygroundComment | None:
    return next(
        (comment for comment in PLAYGROUND_COMMENTS if comment.comment_id == comment_id),
        None,
    )
